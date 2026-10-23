"""Read-only upstream version check for the Skills "可升级" bucket.

Skills CLI GitHub installs are compared the way `skills` does, without running
it: the lock's ``skillFolderHash`` is the git tree SHA of the skill folder, and
GitHub's git trees API returns that SHA. npm coordinates use the public
registry ``latest`` tag. No credentials, no install, no clone, and no writes.
"""

from __future__ import annotations

import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

Fetch = Callable[[str], tuple[int, Any, str]]

ALLOWED_HOSTS = frozenset({"api.github.com", "registry.npmjs.org"})
USER_AGENT = "agents-lab-board"
MAX_BODY = 8_000_000
SUCCESS_TTL = 3600.0
MISS_TTL = 3600.0
FAIL_TTL = 120.0
DEFAULT_MIN_INTERVAL = 0.05
DEFAULT_MAX_REQUESTS = 30
DEFAULT_HOURLY_LIMIT = 50
HOURLY_WINDOW = 3600.0

# The inventory stores the Codex CLI version. That string is the version
# published as this package; there is no separate version feed on the stamp.
RUNTIME_NPM = {"codex": "@openai/codex"}
CANDIDATE_CLASSES = frozenset(
    {
        "skills_cli_managed",
        "runtime_plugin",
        "runtime_builtin",
        "third_party_unmanaged",
        "third_party_customized",
    }
)

_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")
_NPM = re.compile(r"^(@[a-z0-9][a-z0-9._-]{0,63}/)?[a-z0-9][a-z0-9._-]{0,80}$", re.IGNORECASE)
_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[int, Any, str, float]] = {}
_CALLS: list[float] = []


@dataclass(frozen=True)
class UpgradeReport:
    state: str
    detail: str
    updates: dict[str, dict[str, str]]
    checked: int = 0
    skipped: int = 0
    failed: int = 0
    deferred: int = 0
    candidates: int = 0


@dataclass(frozen=True)
class TreeProbe:
    name: str
    owner: str
    repo: str
    ref: str
    skill_path: str
    installed: str


@dataclass(frozen=True)
class ReleaseProbe:
    name: str
    owner: str
    repo: str
    installed: str


@dataclass(frozen=True)
class NpmProbe:
    name: str
    package: str
    installed: str


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()
        _CALLS.clear()


class _GuardRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_GuardRedirects)


def default_fetch(url: str) -> tuple[int, Any, str]:
    """GET one allowlisted URL. Never sends an Authorization header."""
    if not _allowed(url):
        return 0, None, "blocked"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": _accept(url)},
        method="GET",
    )
    try:
        with _OPENER.open(request, timeout=8) as response:
            if not _allowed(response.geturl()):
                return 0, None, "blocked"
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                return 0, None, "too_large"
            return response.status, json.loads(raw.decode("utf-8")), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, ""
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError, OSError, ValueError):
        return 0, None, "network"


def check_upgrades(
    items: list[dict[str, Any]],
    lock_skills: dict[str, Any] | None,
    *,
    repo_root: Path,
    fetch: Fetch | None = None,
    mono: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    max_requests: int = DEFAULT_MAX_REQUESTS,
    hourly_limit: int = DEFAULT_HOURLY_LIMIT,
) -> UpgradeReport:
    """Compare installed coordinates with npm or GitHub. Does not write."""
    transport = fetch or default_fetch
    clock = mono or time.monotonic
    pause = sleep or time.sleep
    lock = lock_skills if isinstance(lock_skills, dict) else {}
    probes: list[TreeProbe | ReleaseProbe | NpmProbe] = []
    candidates = 0
    skipped = 0
    for item in items:
        if not isinstance(item, dict) or not _is_candidate(item):
            continue
        candidates += 1
        probe = _select(item, lock, repo_root)
        if probe is None:
            skipped += 1
        else:
            probes.append(probe)

    budget = _Budget(clock, pause, min_interval, max_requests, hourly_limit)
    updates: dict[str, dict[str, str]] = {}
    checked = 0
    failed = 0
    deferred = 0
    trees: dict[tuple[str, str, str], tuple[dict[str, str] | None, str]] = {}

    def tree_for(probe: TreeProbe) -> tuple[dict[str, str] | None, str]:
        key = (probe.owner, probe.repo, probe.ref)
        if key not in trees:
            trees[key] = _load_tree(probe.owner, probe.repo, probe.ref, transport, budget)
        return trees[key]

    for probe in probes:
        if isinstance(probe, TreeProbe):
            folders, error = tree_for(probe)
            if error == "deferred":
                deferred += 1
                continue
            if error or folders is None:
                failed += 1
                continue
            folder = _folder(probe.skill_path)
            latest = folders.get(folder, "")
            if not _SHA.fullmatch(latest):
                failed += 1
                continue
            checked += 1
            if latest.lower() != probe.installed.lower():
                updates[probe.name] = {"installed": probe.installed, "latest": latest.lower(), "via": "github"}
            continue
        if isinstance(probe, ReleaseProbe):
            latest, error = _load_release(probe.owner, probe.repo, transport, budget)
        else:
            latest, error = _load_npm(probe.package, transport, budget)
        if error == "deferred":
            deferred += 1
            continue
        if error == "unparsed":
            skipped += 1
            continue
        if error or not latest:
            failed += 1
            continue
        checked += 1
        if _newer(latest, probe.installed):
            via = "npm" if isinstance(probe, NpmProbe) else "github"
            updates[probe.name] = {"installed": probe.installed, "latest": latest, "via": via}

    state, detail = _describe(candidates, checked, skipped, failed, deferred, len(updates))
    return UpgradeReport(state, detail, updates, checked, skipped, failed, deferred, candidates)


def _describe(candidates: int, checked: int, skipped: int, failed: int, deferred: int, updates: int) -> tuple[str, str]:
    if failed or deferred:
        if updates:
            return "fallback", f"上游没有全部查完；已确认 {updates} 个可升级"
        if deferred and not failed:
            return "fallback", "已达到本轮请求上限，其余上游未查，不能判断有没有更新"
        return "fallback", "上游请求失败，不能判断有没有更新"
    if checked == 0 and candidates:
        return "fallback", "没有可比较的上游版本，不能当成没有更新"
    if checked == 0:
        return "ok", "没有需要查的上游"
    extra = f"；{skipped} 个没有可比较的版本" if skipped else ""
    if updates:
        return "ok", f"已核对 {checked} 个，{updates} 个可升级{extra}"
    return "ok", f"已核对 {checked} 个，没有更新{extra}"


def _is_candidate(item: dict[str, Any]) -> bool:
    source = _text(item.get("source_class"))
    ref = _text(item.get("baseline_ref"))
    return source in CANDIDATE_CLASSES or ref.startswith(("github:", "npm:"))


def _select(item: dict[str, Any], lock: dict[str, Any], repo_root: Path) -> TreeProbe | ReleaseProbe | NpmProbe | None:
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    entry = lock.get(name)
    entry = entry if isinstance(entry, dict) else None
    source_class = _text(item.get("source_class"))
    baseline_ref = _text(item.get("baseline_ref"))
    installed = _text(item.get("observed_version")) or _text(item.get("current_version"))
    if source_class == "skills_cli_managed" or (entry and _text(entry.get("sourceType")) in {"github", "npm"}):
        return _cli_probe(name, item, entry, baseline_ref, installed)
    if baseline_ref.startswith("github:"):
        return _github_probe(name, baseline_ref, installed, _text(item.get("baseline_subject")))
    if baseline_ref == "runtime:codex":
        version = _semver_text(installed)
        package = RUNTIME_NPM["codex"]
        return NpmProbe(name, package, version) if version else None
    if source_class == "runtime_plugin" or baseline_ref.startswith("plugin:"):
        return _plugin_probe(name, item, repo_root, installed)
    if baseline_ref.startswith("npm:"):
        package = baseline_ref[len("npm:") :]
        version = _semver_text(installed)
        if version and _npm_ok(package):
            return NpmProbe(name, package, version)
    return None


def _cli_probe(
    name: str,
    item: dict[str, Any],
    entry: dict[str, Any] | None,
    baseline_ref: str,
    installed: str,
) -> TreeProbe | ReleaseProbe | NpmProbe | None:
    source_type = _text(entry.get("sourceType")) if entry else ""
    source = _text(entry.get("source")) if entry else ""
    skill_path = _text(entry.get("skillPath")) if entry else ""
    if not skill_path:
        skill_path = _text(item.get("baseline_subject"))
    folder_hash = _text(entry.get("skillFolderHash")) if entry else ""
    if not _SHA.fullmatch(folder_hash):
        folder_hash = installed if _SHA.fullmatch(installed) else ""
    ref = _branch(_text(entry.get("ref")) if entry else "")
    if source_type == "npm":
        version = _semver_text(installed)
        return NpmProbe(name, source, version) if version and _npm_ok(source) else None
    if not source and baseline_ref.startswith("github:"):
        source = baseline_ref[len("github:") :].split("@", 1)[0]
        source_type = "github"
    repo = _parse_repo(source) if source_type == "github" or (source_type == "" and "/" in source) else None
    if repo is None:
        return None
    if _SHA.fullmatch(folder_hash) and skill_path:
        return TreeProbe(name, repo[0], repo[1], ref, skill_path, folder_hash.lower())
    version = _semver_text(installed)
    if version and source_type == "github":
        return ReleaseProbe(name, repo[0], repo[1], version)
    return None


def _github_probe(name: str, baseline_ref: str, installed: str, skill_path: str) -> TreeProbe | ReleaseProbe | None:
    source = baseline_ref[len("github:") :].split("@", 1)[0]
    repo = _parse_repo(source)
    if repo is None:
        return None
    if _SHA.fullmatch(installed) and skill_path:
        return TreeProbe(name, repo[0], repo[1], "", skill_path, installed.lower())
    version = _semver_text(installed)
    return ReleaseProbe(name, repo[0], repo[1], version) if version else None


def _plugin_probe(name: str, item: dict[str, Any], repo_root: Path, installed: str) -> ReleaseProbe | None:
    plugin_id = _plugin_id(_text(item.get("baseline_ref")))
    manifest = _read_manifest(_text(item.get("baseline_subject")), repo_root)
    version = _semver_text(_text(manifest.get("version"))) if manifest else ""
    if not version:
        version = _semver_text(installed)
    repo = _repo_from_manifest(manifest) if manifest else None
    if not plugin_id or not version or repo is None:
        return None
    if repo[1].lower() != plugin_id.lower():
        return None
    return ReleaseProbe(name, repo[0], repo[1], version)


def _plugin_id(baseline_ref: str) -> str:
    if not baseline_ref.startswith("plugin:"):
        return ""
    body = baseline_ref[len("plugin:") :]
    _host, slash, rest = body.partition("/")
    if not slash:
        return ""
    ident, _at, _publisher = rest.partition("@")
    ident = ident.strip()
    if not ident or "/" in ident or ident in {".", ".."}:
        return ""
    return ident


def _read_manifest(subject: str, repo_root: Path) -> dict[str, Any] | None:
    if not subject:
        return None
    try:
        path = _locate(subject, repo_root)
        if not path.is_file():
            return None
        if path.stat().st_size > 262144 or path.suffix.lower() != ".json":
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _locate(subject: str, repo_root: Path) -> Path:
    scripts = Path(__file__).resolve().parents[3] / "skills-governance" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import drift_report

    return drift_report.inventory.locate_path(subject, repo_root)


def _repo_from_manifest(manifest: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("repository", "homepage"):
        found = _repo_from_value(manifest.get(key))
        if found:
            return found
    return None


def _repo_from_value(value: Any) -> tuple[str, str] | None:
    if isinstance(value, dict):
        value = value.get("url") or value.get("repository")
    if not isinstance(value, str):
        return None
    text = value.strip().removesuffix(".git")
    match = re.search(r"github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$", text)
    if not match:
        return None
    return _parse_repo(f"{match.group(1)}/{match.group(2)}")


def _parse_repo(source: str) -> tuple[str, str] | None:
    text = source.strip().removesuffix(".git")
    if not _REPO.fullmatch(text):
        return None
    owner, repo = text.split("/", 1)
    if owner in {".", ".."} or repo in {".", ".."}:
        return None
    return owner, repo


def _branch(value: str) -> str:
    if not value or _SHA.fullmatch(value) or ".." in value.split("/"):
        return ""
    return value if _BRANCH.fullmatch(value) else ""


def _folder(skill_path: str) -> str:
    folder = skill_path.replace("\\", "/")
    lower = folder.lower()
    if lower.endswith("/skill.md"):
        folder = folder[:-9]
    elif lower.endswith("skill.md"):
        folder = folder[:-8]
    return folder[:-1] if folder.endswith("/") else folder


def _npm_ok(package: str) -> bool:
    return bool(package) and ".." not in package and _NPM.fullmatch(package) is not None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _semver_text(value: str) -> str:
    return value if value and _SEMVER.fullmatch(value) else ""


def _pre(text: str) -> tuple:
    parts = []
    for piece in text.split("."):
        if piece.isdigit():
            parts.append((0, int(piece)))
        else:
            parts.append((1, piece))
    return tuple(parts)


def _semver_key(value: str) -> tuple | None:
    match = _SEMVER.fullmatch(value.strip())
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), 1 if pre is None else 0, _pre(pre) if pre else ())


def _newer(latest: str, installed: str) -> bool:
    new_key = _semver_key(latest)
    old_key = _semver_key(installed)
    if new_key is None or old_key is None:
        return False
    return new_key > old_key


class _Budget:
    def __init__(
        self,
        mono: Callable[[], float],
        sleep: Callable[[float], None],
        min_interval: float,
        max_requests: int,
        hourly_limit: int,
    ) -> None:
        self.mono = mono
        self.sleep = sleep
        self.min_interval = min_interval
        self.max_requests = max_requests
        self.hourly_limit = hourly_limit
        self.used = 0
        self.last: float | None = None

    def get(self, url: str, fetch: Fetch) -> tuple[int, Any, str]:
        now = self.mono()
        with _LOCK:
            cached = _cache_get(url, now)
            if cached is not None:
                return cached
            delay = self._reserve(now)
            if delay is None:
                return 0, None, "deferred"
        if delay > 0:
            self.sleep(delay)
        status, body, error = fetch(url)
        if status == 200:
            body = _slim(url, body)
        else:
            body = None
        with _LOCK:
            _CACHE[url] = (status, body, error, self.mono())
        return status, body, error

    def _reserve(self, now: float) -> float | None:
        cutoff = now - HOURLY_WINDOW
        while _CALLS and _CALLS[0] < cutoff:
            _CALLS.pop(0)
        if self.used >= self.max_requests or len(_CALLS) >= self.hourly_limit:
            return None
        delay = 0.0
        if self.last is not None and self.min_interval:
            delay = self.min_interval - (now - self.last)
            if delay < 0:
                delay = 0.0
        self.used += 1
        self.last = now
        _CALLS.append(now)
        return delay


def _cache_get(url: str, now: float) -> tuple[int, Any, str] | None:
    found = _CACHE.get(url)
    if found is None:
        return None
    status, body, error, stored = found
    ttl = SUCCESS_TTL if status == 200 else MISS_TTL if status == 404 else FAIL_TTL
    if now - stored > ttl:
        return None
    return status, body, error


def _slim(url: str, body: Any) -> Any:
    if "/git/trees/" in url and isinstance(body, dict):
        folders: dict[str, str] = {}
        for entry in body.get("tree") or []:
            if not isinstance(entry, dict) or entry.get("type") != "tree":
                continue
            path = entry.get("path")
            sha = entry.get("sha")
            if isinstance(path, str) and isinstance(sha, str) and _SHA.fullmatch(sha):
                folders[path] = sha.lower()
        sha = body.get("sha")
        return {"sha": sha.lower() if isinstance(sha, str) else "", "truncated": body.get("truncated") is True, "folders": folders}
    if isinstance(body, dict) and isinstance(body.get("tag_name"), str):
        return {"tag_name": body["tag_name"]}
    if isinstance(body, dict) and isinstance(body.get("version"), str):
        return {"version": body["version"]}
    if isinstance(body, list):
        names = []
        for item in body:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append({"name": item["name"]})
        return names
    return None


def _load_tree(
    owner: str,
    repo: str,
    ref: str,
    fetch: Fetch,
    budget: _Budget,
) -> tuple[dict[str, str] | None, str]:
    branches: list[str] = []
    if ref:
        branches.append(ref)
    for name in ("HEAD", "main", "master"):
        if name not in branches:
            branches.append(name)
    saw_client_error = False
    for branch in branches:
        status, body, error = budget.get(_tree_url(owner, repo, branch), fetch)
        if error == "deferred":
            return None, "deferred"
        if error or status == 0:
            return None, "network"
        if status == 200 and isinstance(body, dict):
            if body.get("truncated") is True:
                return None, "truncated"
            folders = body.get("folders")
            if not isinstance(folders, dict):
                return None, "bad_payload"
            copied = dict(folders)
            root = body.get("sha")
            if isinstance(root, str) and _SHA.fullmatch(root):
                copied.setdefault("", root.lower())
            return copied, ""
        if status in (403, 429) or status >= 500:
            return None, "http"
        saw_client_error = True
    return None, "http" if saw_client_error else "network"


def _load_release(owner: str, repo: str, fetch: Fetch, budget: _Budget) -> tuple[str, str]:
    status, body, error = budget.get(f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/releases/latest", fetch)
    if error == "deferred":
        return "", "deferred"
    if error or status == 0 or status in (403, 429) or status >= 500:
        return "", "http"
    if status == 200 and isinstance(body, dict):
        tag = _text(body.get("tag_name"))
        if _SEMVER.fullmatch(tag):
            return tag, ""
    status, body, error = budget.get(
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/tags?per_page=20",
        fetch,
    )
    if error == "deferred":
        return "", "deferred"
    if error or status == 0 or status in (403, 429) or status >= 500:
        return "", "http"
    if status != 200 or not isinstance(body, list):
        return "", "http"
    best: tuple | None = None
    best_text = ""
    for item in body:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        key = _semver_key(name)
        if key is not None and (best is None or key > best):
            best = key
            best_text = name
    if not best_text:
        return "", "unparsed"
    return best_text, ""


def _load_npm(package: str, fetch: Fetch, budget: _Budget) -> tuple[str, str]:
    if not _npm_ok(package):
        return "", "unparsed"
    url = f"https://registry.npmjs.org/{quote(package, safe='@')}/latest"
    status, body, error = budget.get(url, fetch)
    if error == "deferred":
        return "", "deferred"
    if error or status == 0 or status in (403, 429) or status >= 500:
        return "", "http"
    if status != 200 or not isinstance(body, dict):
        return "", "http"
    version = _text(body.get("version"))
    if not _SEMVER.fullmatch(version):
        return "", "unparsed"
    return version, ""


def _tree_url(owner: str, repo: str, branch: str) -> str:
    return (
        f"https://api.github.com/repos/{quote(owner)}/{quote(repo)}/git/trees/"
        f"{quote(branch, safe='')}?recursive=1"
    )


def _allowed(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "https" and not parts.username and not parts.password and parts.hostname in ALLOWED_HOSTS


def _accept(url: str) -> str:
    if urlsplit(url).hostname == "api.github.com":
        return "application/vnd.github+json"
    return "application/json"
