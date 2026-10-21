/** Skill 正文读取。纯逻辑，不碰 DOM；后端接口仍是提议契约（见 docs/api-contract.md）。 */

export function contentKey(name, path) {
  return `${name}\n${path}`;
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
  switch (body.state) {
    case "ok":
      if (typeof body.content !== "string") return { state: "error", message: "响应缺少正文 content" };
      if (body.content === "") return { state: "empty", name: body.name, path: body.path };
      return {
        state: "ok",
        name: body.name,
        path: body.path,
        format: body.format || "markdown",
        bytes: body.bytes ?? null,
        sha256: body.sha256 || "",
        content: body.content,
      };
    case "empty":
      return { state: "empty", name: body.name, path: body.path };
    case "missing":
      return { state: "missing", message: body.message || "文件不存在" };
    case "denied":
      return { state: "denied", message: body.message || "无权限读取" };
    case "too_large":
      return { state: "too_large", message: body.message || "文件过大，未载入" };
    case "error":
      return { state: "error", message: body.message || "读取失败" };
    default:
      return { state: "error", message: `未知的 state：${String(body.state)}` };
  }
}

export async function loadSkillContent(fetchImpl, name, path) {
  const url = `/api/skills/content?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}`;
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
  loading: { label: "读取中…", cls: "muted" },
  ok: { label: "ok", cls: "ok" },
  empty: { label: "文件为空", cls: "muted" },
  missing: { label: "文件不存在", cls: "bad" },
  denied: { label: "无权限读取", cls: "warn" },
  too_large: { label: "文件过大，未载入", cls: "warn" },
  error: { label: "读取失败", cls: "bad" },
  unavailable: { label: "后端未提供正文接口（缺口）", cls: "warn" },
};
