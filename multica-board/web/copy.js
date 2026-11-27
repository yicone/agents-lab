/** Shared clipboard and the read-only sentence from design-system.md §7. */

export const READONLY_LINE = "页面只读，不代为执行。复制命令后在自己的终端运行。";
export const COPY_OK = "已复制";
export const COPY_FAILED = "复制失败，请手抄";

/** 有 workspace id 时，资源命令带 --workspace-id。 */
export function multicaCommand(workspaceId, rest) {
  const id = typeof workspaceId === "string" ? workspaceId.trim() : "";
  return id ? `multica --workspace-id ${id} ${rest}` : `multica ${rest}`;
}

/**
 * Three paths: clipboard, then a textarea execCommand, then false.
 * env may inject { clipboard, document } for tests.
 */
export async function copyWithFallback(text, env = {}) {
  const clipboard = env.clipboard !== undefined
    ? env.clipboard
    : (typeof navigator !== "undefined" ? navigator.clipboard : undefined);
  const doc = env.document ?? (typeof document !== "undefined" ? document : undefined);
  try {
    if (clipboard?.writeText) {
      await clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to the textarea path
  }
  try {
    if (!doc?.body || !doc.createElement || !doc.execCommand) return false;
    const ta = doc.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    doc.body.appendChild(ta);
    ta.select();
    const ok = doc.execCommand("copy");
    ta.remove();
    return ok === true;
  } catch {
    return false;
  }
}

export async function markCopy(button, text, env) {
  const ok = await copyWithFallback(text, env);
  if (button) button.textContent = ok ? COPY_OK : COPY_FAILED;
  return ok;
}
