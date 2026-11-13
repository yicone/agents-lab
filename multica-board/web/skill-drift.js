/** Read-only suggestions: never interpolate paths, bodies, or untrusted shell text. */
export const DIGEST_COMMAND = "python3 multica-board/scripts/skills-drift-digest.py --url http://127.0.0.1:8765/api/skills";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function driftSignals(item) {
  const entries = Array.isArray(item?.entries) ? item.entries : [];
  return {
    mismatch: item?.hash_compare?.status === "mismatch",
    drift: ["modified", "missing", "version_changed"].includes(item?.reconciliation_status),
    unrecorded: item?.source_class === "unrecorded" || item?.origin === "unrecorded" || entries.some(entry => entry?.registration === "unrecorded"),
    upgradable: item?.upgrade_status === "update" || item?.bucket === "upgradable",
  };
}

export function driftSuggestions(item) {
  const signals = driftSignals(item);
  if (!Object.values(signals).some(Boolean)) {
    return { commands: [], note: item?.hash_compare?.status === "match"
      ? "无需命令：当前没有漂移或待处理信号。哈希一致不代表门禁通过。"
      : "信息不足：没有可确认的漂移信号，未生成命令。" };
  }
  const commands = [{ label: "读取漂移摘要（在仓库根目录运行）", command: DIGEST_COMMAND }];
  if (signals.drift || signals.unrecorded) commands.push({
    label: "读取本机登记与基线对账（不写清单）",
    command: "python3 skills-governance/scripts/drift_report.py --format text",
  });
  for (const install of Array.isArray(item?.workspace_installs) ? item.workspace_installs : []) {
    if (!UUID.test(install?.workspace_id || "") || !UUID.test(install?.id || "")) continue;
    const command = `multica --workspace-id ${install.workspace_id} skill get ${install.id} --output json`;
    if (!commands.some(entry => entry.command === command)) commands.push({ label: "读取 Workspace Skill 元数据", command });
  }
  return { commands, note: "仅建议只读核查；复制不会执行。hash mismatch 不能判断哪份更新。refresh / update 会写入，须另行批准后人工处理。缺少有效 Workspace ID 时不生成该 Workspace 命令。" };
}
