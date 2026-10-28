/** Skill 正文读取。纯逻辑，不碰 DOM；只读接口契约（见 docs/api-contract.md）。 */

export function contentKey(name, path, file = "SKILL.md") {
  return `${name}\n${path}${file === "SKILL.md" ? "" : `\n${file}`}`;
}

/**
 * 把一次正文请求的结果归一成 {state, ...}。
 * `unavailable` 专指后端没有这个接口（404 unknown endpoint / 405）；
 * `empty` 只表示后端确认读到了一个空文件，绝不用来兜底别的失败。
 */
export function classifyContentResponse({ status, body, networkError }) {
  if (networkError) return { state: "error", message: `网络错误：${networkError}` };
  if (status === 404 && body?.error?.code === "not_found" && body?.error?.message === "unknown endpoint") {
    return { state: "unavailable", message: "后端未提供正文接口" };
  }
  if (status === 405) return { state: "unavailable", message: "后端未提供正文接口" };
  if (typeof status !== "number" || status < 200 || status >= 300) {
    return { state: "error", message: `HTTP ${status ?? "?"}` };
  }
  if (!body || typeof body !== "object") return { state: "error", message: "响应不是 JSON 对象" };
  const metadata = { file: body.file, files: body.files, truncated: body.truncated, source: body.source, collected_at: body.collected_at, evidence_state: body.evidence_state, bytes: body.bytes, content: body.content };
  if (body.state === "ok" && Array.isArray(body.files)) return { ...metadata, state: "ok" };
  switch (body.state) {
    case "ok":
      if (typeof body.content !== "string") return { state: "error", message: "响应缺少正文 content" };
      if (body.content === "") return { ...metadata, state: "empty", name: body.name, path: body.path };
      return {
        ...metadata,
        state: "ok",
        name: body.name,
        path: body.path,
        format: body.format || "markdown",
        bytes: body.bytes ?? null,
        sha256: body.sha256 || "",
        content: body.content,
      };
    case "empty":
      return { ...metadata, state: "empty", name: body.name, path: body.path };
    case "uncollected":
    case "unsupported":
      return { ...metadata, state: body.state, message: body.message };
    case "missing":
      return { state: "missing", message: body.message || "文件不存在" };
    case "denied":
      return { state: "denied", message: body.message || "无权限读取" };
    case "too_large":
      return { state: "too_large", message: body.message || "文件过大，未载入" };
    case "error":
      return { state: "error", message: body.message || "读取失败" };
    case "hash_mismatch":
      return { state: "hash_mismatch", message: body.message || "哈希或字节数不一致" };
    default:
      return { state: "error", message: `未知的 state：${String(body.state)}` };
  }
}

export async function loadSkillContent(fetchImpl, name, path, file = "SKILL.md") {
  const url = `/api/skills/content?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}${file === "@tree" ? "&tree=1" : file === "SKILL.md" ? "" : `&file=${encodeURIComponent(file)}`}`;
  try {
    const res = await fetchImpl(url, { cache: "no-store", headers: { Accept: "application/json" } });
    let body = null;
    try {
      body = await res.json();
    } catch {}
    return classifyContentResponse({ status: res.status, body });
  } catch (err) {
    return classifyContentResponse({ networkError: String(err?.message || err) });
  }
}

export const CONTENT_LABEL = {
  uncollected: { label: "Workspace 正文未采集", cls: "muted" },
  unsupported: { label: "二进制或非 UTF-8 文本，不预览", cls: "muted" },
  loading: { label: "读取中…", cls: "muted" },
  ok: { label: "ok", cls: "ok" },
  empty: { label: "文件为空", cls: "muted" },
  missing: { label: "文件不存在", cls: "bad" },
  hash_mismatch: { label: "校验失败", cls: "bad" },
  denied: { label: "无权限读取", cls: "warn" },
  too_large: { label: "文件过大，未载入", cls: "warn" },
  error: { label: "读取失败", cls: "bad" },
  unavailable: { label: "后端未提供正文接口（缺口）", cls: "warn" },
};
