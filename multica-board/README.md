# multica-board

A local, read-only board across Multica workspaces (ACM-161, phase 1). This README covers the backend (ACM-162). The frontend (ACM-163) goes in `frontend/`, and its build output in `frontend/dist/` is served by the backend.

```text
multica CLI (your login, --output json) → SQLite cache (~/.multica-board) → GET-only JSON API on 127.0.0.1
```

There are no third-party dependencies. It needs Python ≥ 3.10 and `multica` CLI ≥ 0.6.2 on `PATH`.

## Guarantees

- **Loopback only.** The server binds `127.0.0.1` (or `::1`) and refuses any other host. Requests whose `Host` header is not `127.0.0.1:<port>`, `localhost:<port>` or `[::1]:<port>` get `421` (DNS-rebinding guard). A cross-origin `Origin` or `Sec-Fetch-Site: cross-site` gets `403`. No CORS headers are ever sent.
- **No write interface.** The HTTP server answers only `GET`/`HEAD`; every other method returns `405`. The CLI wrapper runs only an exact allow-list (`workspace list`, `workspace member list`, `issue list`, `project list`, `agent list`, `agent tasks`, `runtime list`). Anything else is refused before a process is spawned, and callers cannot inject `--profile`, `--workspace-id` or `--server-url`. There is no "refresh now" endpoint; refresh happens on a timer inside the process.
- **No tokens leak.** The backend never reads the CLI's config or token; the CLI authenticates itself. The following are never cached: issue descriptions, agent instructions, `custom_env`, `custom_args`, `mcp_config`, `runtime_config`, runtime `launch_header`, run output and work dirs, and member emails. Run error text passes through a secret redactor (Bearer/PAT/`m??_` tokens, `sk-`, `ghp_`, JWT, `key=value` secrets, long hex/base64) and is truncated to 300 characters. CLI stderr is redacted the same way before it reaches logs or `sync.errors`. The cache directory is `0700` and its files are `0600`.
- **Every CLI call** uses `--profile <profile> --workspace-id <id>` with `GODEBUG=tlsmlkem=0`. An inherited `MULTICA_WORKSPACE_ID` is stripped. The global default workspace is never changed.

## Run

All commands run from `multica-board/backend`:

```sh
cd multica-board/backend

# 1. See which workspaces your CLI identity can access
python3 -m multica_board workspaces

# 2. One collection pass (prints a per-workspace summary; exit 2 if any workspace failed)
python3 -m multica_board collect --all-workspaces
python3 -m multica_board collect --workspace acm-lab --workspace <uuid>   # explicit allow-list

# 3. Serve the API + frontend, refreshing every 60s
python3 -m multica_board serve --all-workspaces
#   → http://127.0.0.1:8787   (Ctrl-C to stop)

# Serve the existing cache without calling the CLI
python3 -m multica_board serve --all-workspaces --refresh 0

# Tests
python3 -m unittest discover -s tests -v
```

You must pass either `--workspace` (repeatable; takes an ID or slug) or `--all-workspaces`. When a workspace disappears from `workspace list` (left the workspace, or access revoked), its cached rows are purged.

| Flag | Default | Notes |
|---|---|---|
| `--profile` | `desktop-api.multica.ai` | CLI profile. Run this under **your own** login. If `MULTICA_TOKEN` is set in the environment, the CLI uses that token instead. |
| `--db` | `~/.multica-board/board.sqlite` | Delete it to reset the cache. |
| `--refresh` | `60` | Seconds between the end of one cycle and the start of the next. `0` serves the cache only. |
| `--full-every` | `30` | Every Nth cycle re-pages the whole run window instead of stopping at runs already cached. |
| `--run-days` | `14` | Run history window. Runs older than `max(2×, 30)` days are pruned. |
| `--max-task-pages` | `10` | Cap of 200-run pages per agent per cycle. When the cap is hit, `tasks` is reported in `sync.incomplete`. |
| `--concurrency` / `--max-cli` | `3` / `4` | Workspaces (and agents) in parallel / hard cap on concurrent `multica` processes. |
| `--stale-days` | `7` | Open, non-backlog issues idle longer than this are flagged `stale`. |
| `--port` / `--host` | `8787` / `127.0.0.1` | The only other accepted host is `::1`. |
| `--static-dir` | `../frontend/dist` | Served with an SPA fallback to `index.html`. If the directory is missing, `/` returns an endpoint list. |
| `--app-url` | `https://multica.ai` | Base URL for deep links (`/{slug}/issues/{id}`, `/{slug}/agents/{id}`). |

Rough cost: about 20 s per cycle for one workspace with 144 issues and 8 agents. Each CLI call takes 1–3 s; agents and workspaces are fetched in parallel, up to `--max-cli` processes at a time.

To stop a server started in the background, kill the PID it printed (or `lsof -nP -iTCP:8787 -sTCP:LISTEN`). Do not `pkill multica`: that name also matches the workspace daemon.

## API (v0, provisional — to be aligned with ACM-163)

Every endpoint is `GET`, returns JSON, and sends `Cache-Control: no-store`. Timestamps are UTC ISO-8601. Rows from different workspaces are keyed by `workspace.id` plus the resource UUID.

| Endpoint | Query params | Returns |
|---|---|---|
| `/api/health` | — | `{ok, read_only}` |
| `/api/meta` | — | `api_version`, `server` (profile), `generated_at`, `refresh_interval_s`, `stale_issue_days`, `run_window_days`, `last_collect{started_at,finished_at}`, `issue_views[]` |
| `/api/workspaces` | — | `{workspaces:[{id,name,slug,url, sync{last_success_at, stale, errors{resource:msg}, incomplete[], resources{…}}, counts{issues,in_review,blocked,agents}}]}` |
| `/api/board` | `workspace` | `{counts{in_review,blocked,run_failed,stale}, columns{in_review[],blocked[],run_failed[],stale[]}}`. Open issues only; one issue may appear in several columns. |
| `/api/issues` | `view` (`attention`\*, `in_review`, `blocked`, `run_failed`, `stale`, `open`, `all`), `workspace`, `q`, `limit` (≤1000, default 200), `offset` | `{total, limit, offset, view, issues[]}`, sorted by `last_activity_at` desc |
| `/api/agents` | `workspace`, `include_archived=1` | `{agents[]}` (the matrix rows) |
| `/api/runs` | `workspace`, `agent`, `issue`, `status`, `limit` (≤500) | `{runs[]}`, newest first |

`workspace` accepts an ID or a slug. Bad parameters return `400 {error}`; unknown paths return `404`.

**Issue row:** `id, identifier, title, status, status_category, status_name, priority, workspace{id,name,slug}, project{id,title}|null, assignee{type,id,name}|null, parent_issue_id, labels[], due_date, created_at, updated_at, last_activity_at, idle_days, latest_run{id,status,agent{id,name},kind,created_at,completed_at,error_class,error}|null, flags{open,in_review,blocked,run_failed,recovered_failure,stale,run_active}, url`.

**Agent row:** `id, name, status, archived, workspace{…}, runtime{id,name,provider,mode,status,online,last_seen_at,device_info,cli_version}|{id,missing:true}|null, runtime_bound, model{configured|null, inherits_runtime_default, observed[]}, thinking_level|null, service_tier|null, max_concurrent_tasks, load{running,queued}, runs_7d, failures{last_24h,last_7d,last{run_id,issue_id,at,error_class,error,recovered}|null}, last_run_at, url`.

**Run row:** `id, status, agent{id,name}, kind, created_at, started_at, completed_at, error_class, error, workspace{…}, issue_id, issue_url, runtime_id, attempt, max_attempts, models[]`.

### Semantics

- `run_failed` means the issue's **latest** run failed. If an earlier run in the window failed and a later run succeeded, the issue gets `recovered_failure` instead; an old failure is never shown as current.
- `in_review` means awaiting acceptance; `blocked` means blocked. Neither is automatically treated as "waiting on the user". No label or property convention for that exists yet.
- `model.configured: null` means the agent inherits the runtime default, so the actual model is unknown. `model.observed` lists the model names reported in recent run usage.
- `error_class` is one of `quota`, `rate_limit`, `auth`, `network`, `timeout`, `model_unavailable`, `process`, `cancelled`, `unknown`. It is a heuristic over the error text, not a quota balance.
- If a workspace fails mid-cycle, its previous cache stays in place and is marked `sync.stale: true` with `sync.errors`. It is never shown as empty.

## Not in this phase

Batch runtime switching (phase 2), run transcripts, and comment bodies.
