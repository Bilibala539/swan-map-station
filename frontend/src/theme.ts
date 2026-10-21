// 主题管理（R8.1 起步；2026-10-19 用户约定改为：每次加载保障优先暗夜）。
// 亮色仅会话内经顶栏按钮显式切换可达，不跨加载记忆（localStorage 写入保留但加载时不读）。
// URL ?theme=light|dark 仍最高优先——无头自测（R6.5）驱动双主题的入口。
export type Theme = "light" | "dark" | null; // null = 跟随系统（已不用于加载判定）

const KEY = "swan-theme";

export function getStoredTheme(): Theme {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function applyTheme(t: Theme): void {
  try {
    if (t) localStorage.setItem(KEY, t);
    else localStorage.removeItem(KEY);
  } catch { /* 隐私模式等：仅本次生效 */ }
  if (t) document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}

export function effectiveTheme(): "light" | "dark" {
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light") return "light";
  return "dark"; // 每次加载保障优先暗夜（2026-10-19 用户约定）
}

// 切换：在亮/暗之间翻转。当前值取已应用状态（dataset），不再读记忆/系统——
// effectiveTheme 只描述"加载默认"，不描述运行时现状（2026-10-19 约定后两者分离）。
export function toggleTheme(): "light" | "dark" {
  const cur: "light" | "dark" = document.documentElement.dataset.theme === "light" ? "light" : "dark";
  const next = cur === "light" ? "dark" : "light";
  const doc = document as Document & { startViewTransition?: (cb: () => void) => void };
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  if (!doc.startViewTransition || reduced) {
    applyTheme(next);
    return next;
  }
  doc.startViewTransition(() => applyTheme(next));
  return next;
}

// 应用启动时恢复主题（App 首帧前调用）。
// 2026-10-19 起加载恒为暗夜；?theme=light|dark 参数仍可强制（无头自测入口）。
export function initTheme(): void {
  const q = new URLSearchParams(location.search).get("theme");
  if (q === "light" || q === "dark") {
    document.documentElement.dataset.theme = q;
    return;
  }
  document.documentElement.dataset.theme = "dark";
}
