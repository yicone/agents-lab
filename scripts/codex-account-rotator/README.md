# mini 本机 Codex 账号轮换

独立 Python 3 标准库脚本，针对 macOS、CodexBar 0.79.1 的原生 Codex 登录。未涉及 9router、Hermes 或 Multica runtime 配置。默认只打印一行 JSON 决策，不切换、不写持久日志；显式 `--log FILE` 可追加同一行。launchd 模板默认也是 dry-run，通过 stdout 文件保留日志，每 600 秒运行一次。

```sh
python3 scripts/codex-account-rotator/rotate.py --dry-run
# 在宿主机/launchd 环境显式指定 profile（不要在 daemon-managed task 内使用）
python3 scripts/codex-account-rotator/rotate.py --dry-run --multica-profile desktop-api.multica.ai
python3 -m unittest discover -s scripts/codex-account-rotator -v
```

## 决策规则

- 用 `codex-accounts list --json` 唯一标识系统账号，用 `usage --all-accounts` 的精确读数比较。不读取 auth.json、token、cookie，也不回显外部命令的错误内容。
- 仅接受更新时间不超过 5 分钟、未来重置时间、明确 300 分钟 session 与 10080 分钟 weekly 的数据。session 或 weekly **低于** 15% 才考虑切换；目标两个窗口都至少 40%。参数为 `--low` / `--target`。
- 以 `min(session, weekly)` 为余量，先比较 10 个百分点档位，同档优先最早重置，再比较精确余量；最终按账号 ID 稳定排序。这样较近重置有有限优先级，不压过明显更宽裕的账号。
- Free 账号不把月度窗口误判成 session；默认不参与。加 `--allow-credits` 后，仅当没有合格普通账号时，选择余额最多、余额读取成功的 Free 账号。普通付费账号不会因为还有 credits 而忽略耗尽的窗口。当前账号没有有效双窗口时保守不切换（包括已切到 Free 的情况），等待人工处理。
- 批量 usage 退出码 1 可能只是一个账号失败：逐项验证，失效或缺失项记为 `unavailable`；退出码 69、超时、重复邮箱/ID、账号不明、旧数据都不切。外部命令有 90 秒超时。
- Multica daemon 必须明确 running，`active_task_count`、`running_task_count`、`resource_wait_task_count` 都必须是整数 0；当前 CLI 只有汇总计数，没有逐任务 runtime 身份；非零时返回 `task_runtime_unknown` 并禁止切换。不能仅凭没发现 Codex 进程就断言活动任务全为非 Codex。按 runtime 放行的需求尚受此 CLI 数据缺口阻塞，实验版删除计数门禁的做法未采用。还要求 `ps -axo comm=` 中没有名称以 Codex 开头的进程（CodexBar 除外）。桌面 Codex 即使空闲也会阻止，关掉后才可能切换。无法确认状态就不切；不会杀进程或重启任务。
- 脚本文件加非阻塞 flock，防止同一个已安装脚本并发。正式 promote 前重新检查账号清单及空闲状态；上游并发修改保护仍保留。**任务启动与 promote 之间没有共享原子锁**，无法排除检查后瞬间启动的竞态；如果要求严格零竞态，启用 apply 前必须让调度方进入维护窗口，当前脚本不负责暂停调度。不要并行部署多个脚本副本。
- 每次输出时间、模式、当前邮箱前缀、各账号余量/credits/状态、动作和原因；不输出邮箱域名、账号 UUID 或外部原始错误。`skip` 是保守决策，不代表切换成功。额度恢复后只有新启动的 Codex 进程才会使用新账号；脚本不恢复已经失败的任务。

## 安装命令（供批准后执行，本任务未执行）

从仓库根目录执行以下命令，将脚本复制到稳定位置；不要直接让 launchd 引用临时 worktree。需要 PATH 中有可用 Python 3 和 multica。下列脚本通过 plistlib 写绝对路径，兼容路径中的空格。遇到已有安装则停止，避免覆盖。

```sh
python3 - <<'PY'
import os, pathlib, plistlib, shutil, sys
source = pathlib.Path('scripts/codex-account-rotator').resolve()
base = pathlib.Path.home() / 'Library/Application Support/codex-account-rotator'
logs = pathlib.Path.home() / 'Library/Logs/codex-account-rotator'
agent = pathlib.Path.home() / 'Library/LaunchAgents/local.codex-account-rotator.plist'
multica = shutil.which('multica')
profile = 'desktop-api.multica.ai'  # 改为目标宿主机的实际 profile
assert profile.strip(), 'explicit Multica host profile required'
assert multica, 'multica not found'
assert not base.exists() and not agent.exists(), 'existing installation; stop and inspect'
base.mkdir(parents=True, mode=0o700)
logs.mkdir(parents=True, exist_ok=True, mode=0o700)
agent.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source / 'rotate.py', base / 'rotate.py')
with (source / 'local.codex-account-rotator.plist').open('rb') as f:
    config = plistlib.load(f)
replace = {'__PYTHON__': sys.executable, '__SCRIPT__': str(base / 'rotate.py'),
           '__MULTICA__': multica, '__MULTICA_PROFILE__': profile}
config['ProgramArguments'] = [replace.get(x, x) for x in config['ProgramArguments']]
config['StandardOutPath'] = str(logs / 'decisions.jsonl')
config['StandardErrorPath'] = str(logs / 'errors.log')
with agent.open('xb') as f:
    plistlib.dump(config, f)
os.chmod(agent, 0o600)
PY
plutil -lint "$HOME/Library/LaunchAgents/local.codex-account-rotator.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.codex-account-rotator.plist"
```

安装后必须在宿主机环境读回 daemon 状态及首轮日志；profile 无效/不可用时保持 skip，不自动回退其它 profile。任务内 CLI 拒绝 `--profile`，任务内手动 dry-run 请省略该参数；这不能替代 launchd 实测。

先观察 `~/Library/Logs/codex-account-rotator/decisions.jsonl`。日志不会自动轮转，按本机日志保留策略管理。

**正式切换需另行批准**：先 bootout，再把已安装 plist 的唯一 `--dry-run` 改为 `--apply`，重新 bootstrap；不要直接跑原始模板。以下命令未执行：

```sh
launchctl bootout "gui/$(id -u)/local.codex-account-rotator"
python3 - <<'PY'
import pathlib, plistlib
p = pathlib.Path.home() / 'Library/LaunchAgents/local.codex-account-rotator.plist'
with p.open('rb') as f:
    config = plistlib.load(f)
args = config['ProgramArguments']
assert args.count('--dry-run') == 1 and '--apply' not in args
args[args.index('--dry-run')] = '--apply'
with p.open('wb') as f:
    plistlib.dump(config, f)
PY
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.codex-account-rotator.plist"
```

## 回滚命令（未执行）

卸载停止后，保留脚本和日志供检查，把 plist 移出 LaunchAgents：

```sh
launchctl bootout "gui/$(id -u)/local.codex-account-rotator"
python3 - <<'PY'
from pathlib import Path
p = Path.home() / 'Library/LaunchAgents/local.codex-account-rotator.plist'
backup = p.with_suffix('.plist.disabled')
assert not backup.exists(), 'backup exists; inspect first'
p.rename(backup)
PY
```

停止轮换不会自动恢复先前账号。若正式启用后需恢复账号，应先确认无任务，再经批准手动执行 `codexbar codex-accounts promote <原账号的完整邮箱或 UUID>`；原账号取启用前的清单，不从脱敏日志猜测。
