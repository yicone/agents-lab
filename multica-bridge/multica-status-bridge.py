#!/opt/homebrew/bin/python3
"""Poll Multica and enqueue status events; shadow or POST per dest webhook.

Event kinds (each change carries "kind"):
  status       issue newly entered in_review / blocked
  comment        new comment written by an agent (routes with "comment" in events)
  agent_delivery assignee agent's new comment, or a completed run when that
                 agent left no comment and no in_review/blocked transition
                 covered it. ACM Lab sends; other workspaces are shadow_record.
  run_failed     an agent run ended as failed (routes with "run_failed" in events)
  parse_failed JSON/field parse failure (always steward)
  net_alert    workspace network streak >= 6 ticks (~30 min) or recovery

Baseline: a failed/empty `workspace list` skips the scan and keeps state.json as is;
          a failed/unlisted workspace keeps its last snapshot; a workspace without a
          trusted snapshot (ws_base) or with > NULL_BURST unseen in_review/blocked issues
          is snapshotted only, never notified.

Config: ~/.config/multica-bridge/routes.json
        fanout: shadow | send
        shadow = POST steward only (existing webhook); record intended dest
        send   = POST each dest's own endpoint (needs Keychain + approval)
        dest order: before_project title, projects, title_rules,
                    platform_failure, workspaces, fallback
        in_review / blocked are queued only when the latest status_changed
        actor_type is agent. member (and unknown) actors are skipped.
        default/小秘书 is inventory-only, not a silent fallback
Credentials: Keychain service one.yic.multica-bridge account=<endpoint_ref>
             steward may fall back to ~/.config/multica-bridge/webhook.env
State:  ~/.local/state/multica-bridge/state.json  (v2 scan cursor)
Outbox: ~/.local/state/multica-bridge/outbox.json (per-dest circuit, pending/accepted/dead_letter)
Rerun:  ~/.local/state/multica-bridge/rerun.json  (capacity planner; auto_rerun off|dry_run|on,
        on = `issue rerun` for at-capacity failures only, 3 attempts 5/10/20 min, steward notified)
Replay: --replay EVENT_ID... | --replay-dead-letter  [--reset-circuit]
"""
import json, os, re, socket, subprocess, sys, urllib.error, urllib.request, datetime
from urllib.parse import urlparse

HOME = os.path.expanduser("~")
_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARE = os.path.join(HOME, ".local/share/multica-bridge")
# Repo layout keeps modules beside this file. The installed script lives in
# ~/.local/bin and imports the share directory. Never prefer a stale share copy
# when the modules are right here (unit tests and the controlled tree).
if os.path.isfile(os.path.join(_HERE, "outbox.py")):
    sys.path.insert(0, _HERE)
else:
    sys.path.insert(0, _SHARE)
import rerun_capacity as cap
import outbox as ob
import creds
import netfail as nf
import agent_delivery as ad

CONF = os.environ.get("MB_CONF", f"{HOME}/.config/multica-bridge/webhook.env")
STATE = os.environ.get("MB_STATE", f"{HOME}/.local/state/multica-bridge/state.json")
OUTBOX = os.environ.get("MB_OUTBOX", f"{HOME}/.local/state/multica-bridge/outbox.json")
RERUN_STATE = os.environ.get("MB_RERUN_STATE", f"{HOME}/.local/state/multica-bridge/rerun.json")
LOCK = os.environ.get("MB_LOCK", f"{HOME}/.local/state/multica-bridge/bridge.lock")
ROUTES = os.environ.get("MB_ROUTES", f"{HOME}/.config/multica-bridge/routes.json")
NETFAIL = os.environ.get("MB_NETFAIL", f"{HOME}/.local/state/multica-bridge/netfail.json")
PROFILE = "desktop-api.multica.ai"
TARGETS = ("in_review", "blocked")
CLOSED = ("done", "cancelled")
ENV = dict(os.environ, PATH="/Users/tr/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin")
for _k in list(ENV):
    if _k.startswith("MULTICA_"):
        ENV.pop(_k, None)  # always use --profile credentials, never a leaked task token
FIELDS = "id,identifier,title,status,assignee_id,project_id,updated_at,last_activity_at"
CLIP = 900
WS_LIST_KEY = "__workspace_list__"  # netfail streak key for `workspace list` itself
NULL_BURST = 5  # more unseen in_review/blocked issues than this in one workspace = lost baseline

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Do not follow redirects (avoids sending Authorization to another host)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect-disallowed", headers, fp)


def log(msg):
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def ts(s):
    if not s: return 0.0
    s = s.replace("Z", "+00:00")
    if "." in s:  # trim fractional seconds to 6 digits for fromisoformat
        head, rest = s.split(".", 1)
        frac = "".join(ch for ch in rest if ch.isdigit())
        tz = rest[len(frac):]
        s = f"{head}.{frac[:6].ljust(6, '0')}{tz}"
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0

_TIMELINE_TRUNC_RE = re.compile(r"truncat", re.I)


def mc_raw(*args):
    return subprocess.run(["multica", "--profile", PROFILE, *args], cwd="/tmp", env=ENV,
                          capture_output=True, text=True, timeout=90)

def mc(*args):
    out = mc_raw(*args)
    if out.returncode != 0:
        raise RuntimeError(f"multica {' '.join(args)}: {out.stderr.strip()[:300]}")
    if not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"json parse: {e}") from e

def unwrap_timeline(payload, stderr=""):
    """Return (events, complete, reason).

    A blank CLI result, has_more, or a truncation warning on stderr is incomplete.
    An incomplete read must not be used as the latest-event judgment.
    """
    truncated = bool(_TIMELINE_TRUNC_RE.search(stderr or ""))
    if payload is None:
        return [], False, "empty"
    if isinstance(payload, list):
        if truncated:
            return payload, False, "truncated"
        return payload, True, "ok"
    if not isinstance(payload, dict):
        return [], False, "empty"
    events = payload.get("items")
    if events is None:
        events = payload.get("timeline")
    if events is None:
        events = payload.get("events")
    if not isinstance(events, list):
        return [], False, "empty"
    if payload.get("has_more"):
        return events, False, "has_more"
    if truncated:
        return events, False, "truncated"
    return events, True, "ok"

def judge_timeline(payload, stderr, new_status):
    """send, skip, or pending. pending means the cursor must stay put."""
    events, complete, reason = unwrap_timeline(payload, stderr)
    if not complete:
        return {"action": "pending", "reason": reason, "actor": None, "event": None}
    event, ordered = ob.select_latest_status_event(events, new_status)
    if not ordered:
        return {"action": "pending", "reason": "unordered", "actor": None, "event": None}
    actor = None
    if event is not None and event.get("actor_type") is not None:
        actor = str(event.get("actor_type")).strip().lower() or None
    if ob.status_actor_allows(new_status, actor):
        return {"action": "send", "reason": "ok", "actor": actor, "event": event}
    return {"action": "skip", "reason": "actor", "actor": actor, "event": event}

def consider_status(new_status, fetched, prev_t=None, reentry=False):
    """fetched is (payload, stderr, error). error keeps the event pending."""
    payload, stderr, err = fetched
    if err is not None:
        return {"action": "pending", "reason": "unavailable", "actor": None, "event": None}
    decision = judge_timeline(payload, stderr, new_status)
    if decision["action"] != "send" or not reentry:
        return decision
    event = decision.get("event")
    when = ob.parse_ts((event or {}).get("created_at"))
    floor = ob.parse_ts(prev_t)
    if when is None or floor is None or when <= floor:
        return {"action": "skip", "reason": "no_reentry", "actor": decision.get("actor"), "event": event}
    return decision

def hold_pending_issues(current, prev, pending_ids):
    """Keep the previous status and activity for issues still awaiting a timeline."""
    for iid in pending_ids:
        previous = (prev or {}).get(iid)
        if isinstance(previous, dict):
            current[iid] = dict(previous)
        else:
            current.pop(iid, None)

def saved_cursor(prev_t, tick_t, pending_ids):
    """Do not move the activity cursor while a timeline judgment is unfinished."""
    if pending_ids and prev_t:
        return prev_t
    return tick_t

def fetch_issue_timeline(workspace_id, issue_id):
    """(payload, stderr, error). Blank stdout is payload None and error None."""
    try:
        out = mc_raw("--workspace-id", workspace_id, "issue", "timeline", issue_id, "--output", "json")
    except Exception as exc:
        return None, "", exc
    stderr = out.stderr or ""
    if out.returncode != 0:
        detail = stderr.strip()[:300] or f"exit={out.returncode}"
        return None, stderr, RuntimeError(detail)
    stdout = (out.stdout or "").strip()
    if not stdout:
        return None, stderr, None
    try:
        return json.loads(stdout), stderr, None
    except json.JSONDecodeError as exc:
        return None, stderr, exc

def load_conf():
    conf = {}
    if os.path.exists(CONF):
        for line in open(CONF):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip().strip('"').strip("'")
    return conf

def route_for(routes, slug, project_id):
    """Explicit match only — no default/小秘书 fallback (used by outbox drain)."""
    return ob.matched_route(routes, slug, project_id)

def display_bot(routes, slug, project_id, dest, dest_reason):
    spec = ob.endpoints(routes).get(dest) or {}
    if spec.get("bot") or spec.get("bot_id"):
        return spec.get("bot"), spec.get("bot_id")
    r = ob.matched_route(routes, slug, project_id)
    if r:
        return r.get("bot"), r.get("bot_id")
    return None, None

def clip(s):
    s = (s or "").strip()
    return s if len(s) <= CLIP else s[:CLIP] + "…"

def _runs_list(raw):
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return raw.get("runs") or raw.get("items") or []

def attach_dry_run(change, note, extra=None):
    change["dry_run_note"] = note
    if extra:
        change["dry_run"] = extra
    err = change.get("error") or ""
    if note and note not in err:
        change["error"] = (err + "\n" + note).strip() if err else note

def _url_allowed(url):
    try:
        p = urlparse(url)
    except Exception:
        return False
    host = (p.hostname or "").lower()
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if p.scheme == "https":
        return True
    if p.scheme == "http" and loopback:
        return True
    return False

def _exc_name(err):
    if err is None:
        return "error"
    return type(err).__name__


def http_post(url, changes, header=None, timeout=20):
    """POST a batch. Returns (http_status, retry_after, error_kind, exc_type).

    The fourth field is the exception class name on the first transport failure.
    The tuple never includes the URL, header, or exception text.
    """
    if not url:
        return None, None, "no_url", None
    if not _url_allowed(url):
        return None, None, "bad_url", None
    body = json.dumps({"source": "multica-status-bridge", "changes": changes}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": "application/json"})
    if header and ":" in header:
        k, v = header.split(":", 1)
        req.add_header(k.strip(), v.strip())
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, None, None, None
    except urllib.error.HTTPError as e:
        ra = None
        try:
            ra = e.headers.get("Retry-After") if e.headers else None
        except Exception:
            ra = None
        return e.code, ra, f"HTTP {e.code}", "HTTPError"
    except socket.timeout as e:
        return None, None, "timeout", _exc_name(e)
    except TimeoutError as e:
        return None, None, "timeout", _exc_name(e)
    except urllib.error.URLError as e:
        reason = e.reason
        kind = "timeout" if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower() else "network_error"
        return None, None, kind, _exc_name(reason if reason is not None else e)
    except Exception as e:
        return None, None, "network_error", _exc_name(e)

def deliver(routes, box=None, now=None):
    """Drain the outbox (shadow or send). Returns the saved box."""
    box = ob.load_outbox(OUTBOX) if box is None else box
    now = now or cap.utcnow()
    conf = load_conf()
    if ob.fanout_mode(routes) == "shadow":
        n_back = ob.requeue_shadow(box, now, since="2026-10-14T07:33:00Z")
        if n_back:
            log(f"requeue_shadow n={n_back} since=2026-10-14T07:33:00Z")
            ob.save_outbox(OUTBOX, box)

    def poster_for(dest):
        def poster(payloads):
            cred, src = creds.load_cred(dest, conf=conf, steward_dest=ob.steward_dest(routes))
            if not cred:
                return None, None, "no_cred"
            return http_post(cred["url"], payloads, header=cred.get("header"))
        return poster

    for line in ob.drain(box, routes=routes, now=now, poster=lambda *_: (None, None, "no_poster"),
                         poster_for=poster_for, route_for=route_for,
                         on_progress=lambda b: ob.save_outbox(OUTBOX, b)):
        log(line)
    ob.save_outbox(OUTBOX, box)
    return box

def main():
    lock_fd = cap.acquire_lock(LOCK)
    if lock_fd is None:
        log("another instance holds the lock; skip this tick")
        return
    try:
        _main()
    finally:
        cap.release_lock(lock_fd)

def _main():
    try:
        routes = json.load(open(ROUTES)) if os.path.exists(ROUTES) else {}
    except Exception as e:
        log(f"FATAL routes.json: {type(e).__name__}")
        box = ob.load_outbox(OUTBOX)
        now = cap.utcnow()
        ob.enqueue(box, {
            "kind": "parse_failed", "stage": "routes", "bucket": ob.hour_bucket(now),
            "error": "routes.json unreadable",
        }, now, dest=ob.steward_dest({}), dest_reason="exception")
        ob.save_outbox(OUTBOX, box)
        return
    raw = json.load(open(STATE)) if os.path.exists(STATE) else None
    first_run = raw is None
    migrating = (not first_run) and raw.get("_v") != 2
    if first_run:
        prev, seen_c, seen_r, prev_t = {}, [], [], None
    elif migrating:  # v1: {id: {s, w}} for target statuses only
        prev, seen_c, seen_r, prev_t = raw, [], [], None
    else:
        prev, seen_c, seen_r, prev_t = raw["issues"], raw.get("seen_comments", []), raw.get("seen_runs", []), raw.get("t")
    # workspaces with a trusted snapshot; states written before ws_base derive it from issues
    if raw and isinstance(raw.get("ws_base"), list):
        ws_base = set(raw["ws_base"])
    else:
        ws_base = {ent.get("w") for ent in prev.values() if isinstance(ent, dict) and ent.get("w")}
    seen_c_set, seen_r_set = set(seen_c), set(seen_r)
    tick_t = now_iso()
    seed_only = first_run or migrating
    now = cap.utcnow()
    rerun_raw = cap.load_rerun_state(RERUN_STATE)
    rerun_entries = rerun_raw.get("entries") or {}
    parse_acc = {}
    list_ok, list_netfail = set(), {}
    netfail_state = nf.load(NETFAIL)

    def note_parse(workspace, workspace_id, slug, stage, err):
        bucket = ob.hour_bucket(now)
        key = (workspace_id or slug or "unknown", stage, bucket)
        err_s = clip(cap.sanitize_error(str(err), limit=CLIP))
        if key in parse_acc:
            parse_acc[key]["count"] = int(parse_acc[key].get("count") or 1) + 1
            parse_acc[key]["error"] = err_s
        else:
            parse_acc[key] = {
                "kind": "parse_failed", "workspace": workspace,
                "workspace_id": workspace_id, "workspace_slug": slug,
                "stage": stage, "bucket": bucket, "error": err_s, "count": 1,
            }

    def note_collect(workspace, workspace_id, slug, stage, err, ident=None):
        """Network: log only (issue_list counts toward streak). Parse: outbox."""
        kind = nf.classify(err)
        label = ident or workspace or slug or workspace_id
        if kind == "network":
            log(f"WARN net {label} {stage}: {err}")
            if stage == "issue_list" and workspace_id:
                list_netfail[workspace_id] = {
                    "name": workspace, "slug": slug,
                    "error": clip(cap.sanitize_error(str(err), limit=CLIP)),
                }
            return "network"
        log(f"WARN parse {label} {stage}: {err}")
        note_parse(workspace, workspace_id, slug, stage, err)
        return "parse"

    ws_ok = True
    try:
        ws = mc("workspace", "list", "--output", "json")
    except Exception as e:
        ws_ok = False
        if nf.classify(e) == "network":
            log(f"WARN net workspace list: {e}")
            list_netfail[WS_LIST_KEY] = {"name": "(workspace list)", "slug": None,
                                         "error": clip(cap.sanitize_error(str(e), limit=CLIP))}
        else:
            log(f"WARN parse workspace list: {e}")
            note_parse(None, None, None, "workspace_list", e)
        ws = []
    if ws_ok:
        ws = ws if isinstance(ws, list) else (ws.get("workspaces") if isinstance(ws, dict) else None)
        if not isinstance(ws, list):
            ws_ok = False
            log("WARN parse workspace list: unexpected shape")
            note_parse(None, None, None, "workspace_list", TypeError("workspaces field is not a list"))
            ws = []
        elif not ws and prev:
            ws_ok = False
            log("WARN workspace list returned 0 workspaces; keeping last snapshot")
    if not ws_ok:
        # never overwrite the baseline from a failed/empty listing
        if not seed_only:
            box = ob.load_outbox(OUTBOX)
            evs = list(parse_acc.values()) + nf.observe(netfail_state, ok_ids=[], netfail=list_netfail, now=now)
            for c in evs:
                dest, reason = ob.resolve_dest(c, routes)
                ob.enqueue(box, c, now, dest=dest, dest_reason=reason, routes=routes)
            ob.save_outbox(OUTBOX, box)
            nf.save(NETFAIL, netfail_state)
        log(f"scan skipped; keeping last snapshot ({len(prev)} issue(s)); state not written")
        deliver(routes)
        return
    list_ok.add(WS_LIST_KEY)
    listed_ws = {w["id"] for w in ws}
    current, meta, failed_ws = {}, {}, set()
    for w in ws:
        offset = 0
        pages_ok = False
        while True:
            try:
                d = mc("--workspace-id", w["id"], "issue", "list", "--limit", "100", "--offset", str(offset),
                       "--output", "json", "--fields", FIELDS)
            except Exception as e:
                note_collect(w.get("name"), w.get("id"), w.get("slug"), "issue_list", e)
                failed_ws.add(w["id"]); break
            items = d.get("issues", []) if isinstance(d, dict) else None
            if not isinstance(items, list):
                note_parse(w.get("name"), w.get("id"), w.get("slug"), "issue_list",
                           TypeError("issues field is not a list"))
                failed_ws.add(w["id"]); break
            for i in items:
                current[i["id"]] = {"s": i["status"], "w": w["id"], "a": i.get("last_activity_at")}
                meta[i["id"]] = dict(i, workspace=w["name"], workspace_id=w["id"], workspace_slug=w.get("slug"))
            if not d.get("has_more") or not items:
                pages_ok = True
                break
            offset += len(items)
        if pages_ok:
            list_ok.add(w["id"])

    # a failed (even partially) or unlisted workspace is skipped whole: drop its partial
    # pages and carry its last snapshot, so a transient error never resets its baseline
    skip_ws = failed_ws | ({ent.get("w") for ent in prev.values()} - listed_ws)
    for iid in [k for k, v in current.items() if v["w"] in skip_ws]:
        current.pop(iid); meta.pop(iid, None)
    for iid, ent in prev.items():
        if ent.get("w") in skip_ws:
            current[iid] = ent; meta[iid] = {"workspace_id": ent.get("w"), "_carried": True}
    # workspaces listed OK for the first time (or after a lost baseline) are seed-only
    fresh_ws = set() if seed_only else {w for w in list_ok if w != WS_LIST_KEY and w not in ws_base}
    for wid in sorted(fresh_ws):
        n = sum(1 for v in current.values() if v["w"] == wid)
        log(f"baseline seeded workspace={wid} issues={n}; no events this tick")
    new_base = {w for w in list_ok if w != WS_LIST_KEY} | (ws_base & skip_ws)

    changes = []
    shadow_deliveries = []
    agent_names = {}
    runs_cache = {}

    def base(iid, kind):
        m = meta[iid]
        dest, reason = ob.resolve_dest(
            {"kind": kind, "workspace_slug": m.get("workspace_slug"),
             "project_id": m.get("project_id")},
            routes,
        )
        bot, bot_id = display_bot(routes, m.get("workspace_slug"), m.get("project_id"), dest, reason)
        return {"kind": kind, "workspace": m.get("workspace"), "identifier": m.get("identifier"),
                "title": m.get("title"), "status": m.get("status"), "assignee_id": m.get("assignee_id"),
                "workspace_slug": m.get("workspace_slug"), "workspace_id": m.get("workspace_id"),
                "project_id": m.get("project_id"), "issue_id": iid,
                "route_bot": bot, "route_bot_id": bot_id,
                "dest": dest, "dest_reason": reason, "notify_only": True}

    def names(wid):
        if wid not in agent_names:
            try:
                ags = mc("--workspace-id", wid, "agent", "list", "--output", "json") or []
                ags = ags if isinstance(ags, list) else ags.get("agents", [])
                agent_names[wid] = {a["id"]: a.get("name") for a in ags}
            except Exception as e:
                log(f"WARN agent list {wid}: {e}"); agent_names[wid] = {}
        return agent_names[wid]

    def fetch_runs(iid, m):
        if iid in runs_cache:
            return runs_cache[iid]
        try:
            runs = _runs_list(mc("--workspace-id", m["workspace_id"], "issue", "runs", iid, "--output", "json"))
        except Exception as e:
            note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                         "runs_list", e, ident=m.get("identifier"))
            runs = []
        runs_cache[iid] = runs
        return runs

    pending_ids = set()

    def open_status(m, c, reentry=False):
        """send, skip, or pending. pending keeps this issue's status and activity."""
        iid = c.get("issue_id")
        fetched = fetch_issue_timeline(m.get("workspace_id"), iid)
        if fetched[2] is not None:
            note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                         "timeline", fetched[2], ident=m.get("identifier"))
        decision = consider_status(c.get("new_status"), fetched, prev_t=prev_t, reentry=reentry)
        c["actor_type"] = decision.get("actor")
        if decision["action"] == "pending":
            pending_ids.add(iid)
            log(f"pending status {c.get('identifier')} {c.get('new_status')} reason={decision.get('reason')}")
            return "pending"
        if decision["action"] == "send":
            event = decision.get("event") or {}
            if event.get("created_at"):
                c["updated_at"] = event.get("created_at")
            if reentry:
                c["reentered"] = True
                c["old_status"] = ((event.get("details") or {}).get("from")) or c.get("old_status")
            return "send"
        log(f"skip status {c.get('identifier')} {c.get('new_status')} actor_type={c.get('actor_type') or 'unknown'}")
        return "skip"

    # 1) status changes (only when previously known; v1 state already holds target statuses)
    if not first_run:
        unseen = {}  # wid -> [change] for issues absent from the baseline (old_status=None)
        for iid, ent in current.items():
            m = meta[iid]
            if m.get("_carried") or ent["s"] not in TARGETS or ent["w"] in fresh_ws:
                continue
            pe = prev.get(iid, {})
            old = pe.get("s")
            if old != ent["s"]:
                c = base(iid, "status"); c.update(old_status=old, new_status=ent["s"], updated_at=m.get("updated_at"))
                if old is None:
                    unseen.setdefault(ent["w"], []).append(c)
                elif open_status(m, c) == "send":
                    changes.append(c)
            elif pe and prev_t and ent.get("a") and ent.get("a") != pe.get("a"):
                # same status at both ticks but activity moved: catch round trips
                # (e.g. in_review -> in_progress -> in_review) inside one poll window
                c = base(iid, "status")
                c.update(old_status=old, new_status=ent["s"], updated_at=ent.get("a"), reentered=True)
                if open_status(m, c, reentry=True) == "send":
                    changes.append(c)
        for wid, cs in unseen.items():
            if len(cs) > NULL_BURST:
                log(f"WARN baseline rebuild workspace={wid}: {len(cs)} unseen issue(s) in "
                    f"{'/'.join(TARGETS)}; snapshot only, not notified")
                fresh_ws.add(wid)
            else:
                for c in cs:
                    owner = meta.get(c.get("issue_id")) or {}
                    if open_status(owner, c) == "send":
                        changes.append(c)

    # 1b) assignee delivery the status pass did not already cover.
    #     Only issues whose activity moved since the stored cursor, so the first
    #     enabled tick does not replay history.
    def collect_deliveries():
        if seed_only:
            return
        status_cover = {}
        for c in changes:
            if c.get("kind") == "status" and c.get("new_status") in TARGETS and c.get("issue_id"):
                status_cover.setdefault(c["issue_id"], []).append(
                    {"to": c.get("new_status"), "at": c.get("updated_at")}
                )
        for iid, ent in current.items():
            m = meta.get(iid) or {}
            if m.get("_carried") or ent["w"] in fresh_ws:
                continue
            p = prev.get(iid, {})
            if not ent.get("a") or ent.get("a") == p.get("a"):
                continue
            _r = ob.matched_route(routes, m.get("workspace_slug"), m.get("project_id"))
            if ad.route_policy(m.get("workspace_slug"), _r) == "off":
                continue
            assignee = m.get("assignee_id")
            if not assignee or assignee not in names(m["workspace_id"]):
                continue
            if ad.is_quiet(m.get("status")):
                continue
            since = p.get("a") or prev_t
            prev_ts = ts(p.get("a"))
            try:
                raw_cs = mc("--workspace-id", m["workspace_id"], "issue", "comment", "list", iid,
                            "--since", since, "--compact", "--output", "json") if since else []
            except Exception as e:
                note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                             "comment_list", e, ident=m.get("identifier"))
                raw_cs = []
            if isinstance(raw_cs, dict):
                raw_cs = raw_cs.get("comments") or raw_cs.get("items") or []
            comments = []
            for cm in raw_cs or []:
                if not isinstance(cm, dict):
                    continue
                created = cm.get("created_at")
                comments.append({
                    "id": cm.get("id"),
                    "author_type": cm.get("author_type"),
                    "author_id": cm.get("author_id"),
                    "comment_type": cm.get("comment_type") or cm.get("type"),
                    "created_at": created,
                    "content": cm.get("content"),
                    "is_new": bool(cm.get("id")) and cm.get("id") not in seen_c_set and ts(created) > prev_ts,
                })
            payload = {
                "issue_id": iid,
                "identifier": m.get("identifier"),
                "title": m.get("title"),
                "status": m.get("status"),
                "assignee_type": "agent",
                "assignee_id": assignee,
                "workspace": m.get("workspace"),
                "workspace_slug": m.get("workspace_slug"),
                "workspace_id": m.get("workspace_id"),
                "project_id": m.get("project_id"),
                "comments": comments,
                "runs": [],
                "transitions": list(status_cover.get(iid) or []),
                "same_tick_covered": iid in status_cover,
            }
            change = ad.collect_for_issue(payload, _r)
            if change is None and not payload["same_tick_covered"]:
                payload["runs"] = []
                for r in fetch_runs(iid, m):
                    if not isinstance(r, dict):
                        continue
                    completed = r.get("completed_at")
                    payload["runs"].append({
                        "id": r.get("id"),
                        "agent_id": r.get("agent_id"),
                        "status": r.get("status"),
                        "completed_at": completed,
                        "is_new": bool(r.get("id")) and ts(completed) > prev_ts,
                    })
                change = ad.collect_for_issue(payload, _r)
            if change is None:
                continue
            if not payload["same_tick_covered"]:
                try:
                    tl = mc("--workspace-id", m["workspace_id"], "issue", "timeline", iid, "--output", "json") or []
                    tl = tl if isinstance(tl, list) else (tl.get("items") or tl.get("timeline") or [])
                except Exception as e:
                    note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                                 "timeline", e, ident=m.get("identifier"))
                    tl = None
                if tl is not None:
                    extra = []
                    for ev in tl:
                        if not isinstance(ev, dict) or ev.get("action") != "status_changed":
                            continue
                        to = (ev.get("details") or {}).get("to")
                        if to in TARGETS:
                            extra.append({"to": to, "at": ev.get("created_at")})
                    payload["transitions"] = list(status_cover.get(iid) or []) + extra
                    change = ad.collect_for_issue(payload, _r)
                    if change is None:
                        continue
            if change.get("delivery") == "send":
                changes.append(change)
                log(f"agent_delivery send {m.get('identifier')} {change.get('event_id')} {change.get('reason')}")
            else:
                shadow_deliveries.append(change)
                log(f"agent_delivery shadow {m.get('identifier')} {change.get('event_id')} {change.get('reason')}")

    hold_pending_issues(current, prev, pending_ids)
    collect_deliveries()

    # 2) agent comments (opt-in) + 3) failed runs (opt-in; dest forced to steward)
    floor_t = (ts(prev_t) - 900) if prev_t else None
    for iid, ent in current.items():
        m = meta[iid]
        if m.get("_carried"):
            continue
        ws_seed = seed_only or ent["w"] in fresh_ws
        _r = ob.matched_route(routes, m.get("workspace_slug"), m.get("project_id"))
        events = _r.get("events", [])
        if not events:
            continue
        p = prev.get(iid, {})
        if "comment" in events and not ws_seed and ent.get("a") and ent.get("a") != p.get("a"):
            since = p.get("a") or prev_t
            try:
                cs = mc("--workspace-id", m["workspace_id"], "issue", "comment", "list", iid,
                        "--since", since, "--compact", "--output", "json") if since else []
            except Exception as e:
                note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                             "comment_list", e, ident=m.get("identifier"))
                cs = []
            for cm in (cs or []):
                if not isinstance(cm, dict):
                    continue
                ctype = str(cm.get("comment_type") or cm.get("type") or "").lower()
                if ctype == "system":
                    if cm.get("id") and cm.get("id") not in seen_c_set:
                        seen_c.append(cm["id"]); seen_c_set.add(cm["id"])
                    continue
                if cm.get("author_type") != "agent" or cm.get("id") in seen_c_set:
                    continue
                seen_c.append(cm["id"]); seen_c_set.add(cm["id"])
                c = base(iid, "comment")
                c.update(comment_id=cm["id"], author=names(m["workspace_id"]).get(cm.get("author_id"), cm.get("author_id")),
                         created_at=cm.get("created_at"), content=clip(cm.get("content")))
                changes.append(c)
        if "run_failed" in events and ent["s"] not in CLOSED:
            runs = fetch_runs(iid, m)
            for r in runs:
                if r.get("status") != "failed" or r.get("id") in seen_r_set:
                    continue
                seen_r.append(r["id"]); seen_r_set.add(r["id"])
                if ws_seed or floor_t is None or ts(r.get("completed_at")) < floor_t:
                    continue
                c = base(iid, "run_failed")
                err = cap.sanitize_error(r.get("error"), limit=CLIP)
                c.update(run_id=r["id"], agent=names(m["workspace_id"]).get(r.get("agent_id"), r.get("agent_id")),
                         completed_at=r.get("completed_at"), error=err,
                         failure_reason=r.get("failure_reason"), trigger_summary=clip(r.get("trigger_summary")))
                mode, mode_warn = cap.auto_rerun_mode(routes, _r)
                if mode_warn:
                    log(mode_warn)
                if mode != "off" and not seed_only and cap.is_capacity_failure(r.get("failure_reason"), r.get("error")):
                    if cap.is_autopilot_run(r):
                        log(f"{cap._prefix(mode)}: {m.get('identifier')} run {r['id']} is an Autopilot task; not rerun")
                    else:
                        entry, ev = cap.upsert_from_failure(
                            rerun_entries, issue_id=iid, agent_id=r.get("agent_id"), run_id=r["id"],
                            failure_reason=r.get("failure_reason"), error=r.get("error"),
                            completed_at=r.get("completed_at"), identifier=m.get("identifier"),
                            workspace_id=m.get("workspace_id"), workspace_slug=m.get("workspace_slug"),
                            now=now,
                        )
                        if entry:
                            if ev == "exhausted_hold":
                                note = cap.plan_note(entry, "exhausted_hold", mode)
                            elif ev == "updated":
                                n_next = int(entry.get("attempt") or 0) + 1
                                plan = (f"next retry at {entry.get('next_retry_at')} as attempt {n_next}/{cap.MAX_ATTEMPTS}"
                                        if n_next <= cap.MAX_ATTEMPTS else
                                        f"budget used ({cap.MAX_ATTEMPTS}/{cap.MAX_ATTEMPTS}); exhausted check")
                                note = (f"{cap._prefix(mode)}: retarget {entry.get('identifier')} "
                                        f"to run {entry.get('run_id')}; {plan}.")
                            else:
                                note = cap.plan_note(entry, "scheduled", mode)
                            attach_dry_run(c, note, {
                                "event": ev, "attempt": int(entry.get("attempt") or 0),
                                "max_attempts": cap.MAX_ATTEMPTS, "next_retry_at": entry.get("next_retry_at"),
                                "run_id": entry.get("run_id"), "mode": mode,
                            })
                            log(note)
                changes.append(c)

    def save_rerun():
        cap.save_atomic(RERUN_STATE, {"_v": 1, "t": tick_t, "entries": rerun_entries})

    def notify_steward(iid, m, entry, ev, mode):
        """One outbox item per rerun attempt / exhaustion, saved immediately (survives a later crash)."""
        c = base(iid, "run_failed")
        note = ev["note"]
        c.update(
            event_id=cap.notify_event_id(entry, ev["event"], ev.get("attempt")),
            run_id=entry.get("run_id"),
            agent=names(m.get("workspace_id")).get(entry.get("agent_id"), entry.get("agent_id")),
            completed_at=None,
            error=(note + "\n" + (entry.get("error_summary") or "")).strip(),
            failure_reason=entry.get("failure_reason"),
            trigger_summary="auto_rerun",
            auto_rerun={
                "event": ev["event"], "attempt": int(entry.get("attempt") or 0),
                "max_attempts": cap.MAX_ATTEMPTS, "next_retry_at": entry.get("next_retry_at"),
                "failed_run_id": entry.get("run_id"), "new_run_id": ev.get("new_run_id"),
                "confirmed": ev.get("confirmed"), "mode": mode, "note": note,
            },
        )
        box = ob.load_outbox(OUTBOX)
        dest, reason = ob.resolve_dest(c, routes)
        emode = ob.enqueue_mode(c, routes, ob.endpoints(routes).get(dest) or {})
        res = ob.enqueue(box, c, now, mode=emode, dest=dest, dest_reason=reason, routes=routes)
        ob.save_outbox(OUTBOX, box)
        log(f"AUTO-RERUN notify dest={dest} event_id={c['event_id']} {res}")

    # 4) capacity rerun planner: dry_run plans and logs; on runs `issue rerun` after a fresh pre-flight.
    #    Never comments and never changes issue status.
    if not seed_only:
        rerun_entries = cap.prune_entries(rerun_entries, now)
        for entry in list(rerun_entries.values()):
            if entry.get("outcome") not in (None, "pending", "exhausted"):
                continue
            iid = entry.get("issue_id")
            m = meta.get(iid) or {}
            if m.get("_carried") or (m.get("workspace_id") in skip_ws):
                continue
            _r = ob.matched_route(routes, m.get("workspace_slug") or entry.get("workspace_slug"), m.get("project_id"))
            mode, _ = cap.auto_rerun_mode(routes, _r)
            if mode == "off":
                continue
            if iid not in current:
                continue
            if not m.get("workspace_id"):
                continue
            if cap.recover_inflight(entry, now):
                log(f"AUTO-RERUN: {entry.get('identifier')} interrupted rerun attempt "
                    f"{entry.get('attempt')} recovered; attempt counted, next {entry.get('next_retry_at')}")
                save_rerun()
            runs = fetch_runs(iid, m)
            nxt = cap.parse_ts(entry.get("next_retry_at"))
            if nxt and now >= nxt and (entry.get("attempt") or 0) < cap.MAX_ATTEMPTS:
                try:
                    active = _runs_list(mc("--workspace-id", m["workspace_id"], "issue", "runs", iid,
                                           "--active", "--output", "json"))
                    if active:
                        seen_ids = {x.get("id") for x in runs}
                        runs = runs + [a for a in active if a.get("id") not in seen_ids]
                except Exception as e:
                    note_collect(m.get("workspace"), m.get("workspace_id"), m.get("workspace_slug"),
                                 "runs_list", e, ident=m.get("identifier") or iid)
            action, reason = cap.evaluate_gates(
                entry,
                issue_status=current[iid]["s"],
                assignee_id=m.get("assignee_id"),
                runs=runs,
                now=now,
            )
            if action == "fire" and mode == "on":
                ev, action, reason = cap.execute_rerun(entry, mc=mc, now=now, save=save_rerun)
            else:
                ev = cap.apply_gate(entry, action, reason, now=now, mode=mode,
                                    newest_run=reason if action == "retarget" else None)
            if ev is None:
                continue
            log(ev["note"])
            if mode == "on":
                if ev["event"] in ("rerun", "rerun_error", "exhausted"):
                    save_rerun()
                    notify_steward(iid, m, entry, ev, mode)
                continue
            events = _r.get("events", [])
            if "run_failed" not in events:
                continue
            if ev["event"] in ("skipped", "cleared", "retargeted"):
                continue
            c = base(iid, "run_failed")
            c.update(
                run_id=entry.get("run_id"),
                agent=names(m.get("workspace_id")).get(entry.get("agent_id"), entry.get("agent_id")),
                completed_at=None,
                error=entry.get("error_summary") or "",
                failure_reason=entry.get("failure_reason"),
                trigger_summary="auto_rerun dry-run",
                dry_run_event=ev["event"],
            )
            attach_dry_run(c, ev["note"], {
                "event": ev["event"], "attempt": int(entry.get("attempt") or 0),
                "max_attempts": cap.MAX_ATTEMPTS, "next_retry_at": entry.get("next_retry_at"),
                "run_id": entry.get("run_id"), "mode": mode, "reason": ev.get("reason"),
            })
            changes.append(c)

    if not seed_only:
        for pf in parse_acc.values():
            changes.append(pf)
        for ev in nf.observe(netfail_state, ok_ids=list_ok, netfail=list_netfail, now=now):
            changes.append(ev)
        nf.save(NETFAIL, netfail_state)

    box = ob.load_outbox(OUTBOX)
    n_shadow = n_shadow_dup = 0
    for c in shadow_deliveries:
        res = ad.remember_shadow(box, c, now_iso())
        if res == "duplicate":
            n_shadow_dup += 1
        else:
            n_shadow += 1
    if shadow_deliveries:
        log(f"agent_delivery shadow_record n={n_shadow} dup={n_shadow_dup}")
    n_mute = ob.mute_network_misfiled(box, now)
    if n_mute:
        log(f"muted network-misfiled parse_failed n={n_mute}")
    stats = {"added": 0, "merged": 0, "duplicate": 0, "muted": 0}
    fanout = ob.fanout_mode(routes)
    if not seed_only:
        for c in changes:
            dest, reason = ob.resolve_dest(c, routes)
            if ob.is_exception(c):
                mode = ob.enqueue_mode(c, routes, ob.endpoints(routes).get(dest) or {})
            else:
                mode = ob.delivery_mode(routes, ob.matched_route(routes, c.get("workspace_slug"), c.get("project_id")))
            ev = ob.enqueue(box, c, now, mode=mode, dest=dest, dest_reason=reason, routes=routes)
            stats[ev] = stats.get(ev, 0) + 1

    hold_pending_issues(current, prev, pending_ids)
    state_obj = {"_v": 2, "t": saved_cursor(prev_t, tick_t, pending_ids),
                 "issues": current, "ws_base": sorted(new_base),
                 "seen_comments": seen_c[-2000:], "seen_runs": seen_r[-2000:]}
    crash = os.environ.get("MB_CRASH_AFTER")
    ob.persist_two_phase(OUTBOX, box, STATE, state_obj, crash=crash)
    cap.save_atomic(RERUN_STATE, {"_v": 1, "t": tick_t, "entries": rerun_entries})

    if stats.get("added") or stats.get("merged") or stats.get("muted") or stats.get("duplicate"):
        log(f"enqueued fanout={fanout} added={stats['added']} merged={stats['merged']} "
            f"dup={stats['duplicate']} muted={stats['muted']}")

    box = deliver(routes, box, now)
    ct = ob.counts(box)
    dests = ",".join(sorted(ct.get("by_dest") or {"steward": {}})) or "none"
    if seed_only:
        log(("seeded " if first_run else "migrated+seeded ") + f"{len(current)} issue(s); "
            f"outbox fanout={fanout} dests={dests} pending={ct['pending']}")
    elif not changes and ct["pending"] == 0:
        log(f"no changes; tracking {len(current)} issue(s); "
            f"outbox fanout={fanout} dests={dests} pending=0 accepted={ct['accepted']} "
            f"shadowed={ct['shadowed']} dead_letter={ct['dead_letter']}")
    else:
        log(f"outbox fanout={fanout} dests={dests} pending={ct['pending']} accepted={ct['accepted']} "
            f"shadowed={ct['shadowed']} dead_letter={ct['dead_letter']} muted={ct['muted']}")

def cmd_replay(argv):
    reset = "--reset-circuit" in argv
    dead_all = "--replay-dead-letter" in argv
    ids = [a for a in argv if not a.startswith("-") and a not in ("--replay",)]
    box = ob.load_outbox(OUTBOX)
    now = ob.utcnow()
    if dead_all:
        n = ob.replay(box, None, now, reset_circuit=reset)
    elif ids:
        n = ob.replay(box, ids, now, reset_circuit=reset)
    else:
        log("usage: multica-status-bridge.py --replay EVENT_ID [EVENT_ID...]")
        log("       multica-status-bridge.py --replay-dead-letter [--reset-circuit]")
        sys.exit(2)
    ob.save_outbox(OUTBOX, box)
    ct = ob.counts(box)
    log(f"requeued n={n} pending={ct['pending']} "
        f"dead_letter={ct['dead_letter']} circuit_reset={reset}")
    log("next launchd tick will send (or shadow); do not re-POST accepted events")

if __name__ == "__main__":
    try:
        if any(a in sys.argv for a in ("--replay", "--replay-dead-letter")):
            cmd_replay(sys.argv[1:])
        else:
            main()
    except InterruptedError:
        log("FATAL interrupted after outbox write; cursor not advanced")
        sys.exit(1)
    except Exception as e:
        log(f"FATAL {e}")
        sys.exit(1)
