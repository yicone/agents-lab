# Devin Session ↔ Workspace 调查与修复

本目录专门记录 Devin Desktop / Devin CLI 在本机上的 Session 与工作目录对应关系问题。

## 已确认的数据模型

对 local `devin-cli` 会话，主目录来自 `sessions.working_directory`；附加目录来自
`sessions.workspace_dirs`。Devin Desktop 会在自己的状态库中镜像附加目录到：

```text
info._meta["cognition.ai/additionalWorkspaceDirs"]
```

Desktop 根据主目录和附加目录的去重并集生成 Agent's working directories 与 Explorer 的多根工作区。
因此修复必须同时保持 Devin CLI 来源和 Desktop 镜像一致。

本工具只处理这两个本地 SQLite 存储层，不修改项目文件、Session transcript、Space 分组或共享
`workspace.json`。默认路径是 macOS 本机的：

```text
~/.local/share/devin/cli/sessions.db
~/Library/Application Support/Devin/User/globalStorage/state.vscdb
```

## 使用流程

### 1. 只读调查

```bash
python3 docs/research/devin-session-workspace/tools/repair_session_workspace.py inspect \
  --session accurate-opossum \
  --session garrulous-haddock \
  --session wistful-bowl \
  --session lumbar-success
```

`inspect` 不写入任何数据，并同时显示 CLI 来源与 Desktop 镜像。

### 2. 预备修复

先退出 Devin Desktop 和 Devin CLI。修复工具会拒绝修改仍被 `lsof` 持有的数据库。

```bash
python3 docs/research/devin-session-workspace/tools/repair_session_workspace.py repair \
  --session accurate-opossum \
  --session garrulous-haddock \
  --session wistful-bowl \
  --session lumbar-success \
  --expect-cwd /Users/tr/Workspace/elder-oasis \
  --expect-extra-json '["/Users/tr/Workspace/yr"]' \
  --set-extra-json '[]'
```

未加 `--apply` 时只进行校验和打印计划，不写数据库。确认计划无误后，再加 `--apply`。
工具会备份数据库及存在的 `-wal` / `-shm` sidecar，并生成 SHA-256 manifest。

修复后重新启动 Devin，逐一打开目标 Session，确认 Explorer 只显示预期目录；随后再次运行
`inspect` 做存储层回读。

### 3. 回退边界

备份目录是恢复入口。不要在 Devin 运行时直接替换 SQLite 主库或 WAL；如需回退，应先退出
所有 Devin 写入进程，再根据 manifest 选择对应备份恢复，并重新冷启动验证。

## 证据与已知边界

- `workspace_dirs` 是主目录之外的附加目录列表，不是主目录本身。
- `additionalWorkspaceDirs` 是 Desktop 镜像；清理 CLI 来源但保留该字段可能被 Desktop 重新采用。
- Space 的成员目录并集与会话级附加目录是两个概念。本工具不修改 Space 成员关系。
- 当前没有证据可以把某次 `yr` 写入归因到一个确定的 UI 点击；“共享 Space 目录被回写到成员
  Session”是需要后续版本回归或日志证据验证的假设。

## 目录

- `cases/`：逐案事实、证据、修复和未决假设。
- `tools/`：只读检查与显式授权修复工具。
- `tests/`：只使用临时 SQLite fixture 的自动化测试。
