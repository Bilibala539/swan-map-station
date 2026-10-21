import { useEffect, useRef, useState } from "react";
import type { Meta } from "../types";
import type { ViewName } from "../App";
import { effectiveTheme, toggleTheme } from "../theme";
import { IconMoon, IconSun } from "./Icons";

interface Props {
  meta?: Meta;      // 元信息已迁侧栏（side-foot），保留可选兼容旧调用
  view?: ViewName;
  onView?: (v: ViewName) => void;
  onSearch: (id: string) => boolean;
}

// 导航已迁往侧栏（Sidebar.tsx）；Topbar 只留 logo / 搜索 / 主题切换
export default function Topbar({ onSearch }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(effectiveTheme());

  // Linear 式："/" 键聚焦搜索（输入态不劫持）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.defaultPrevented) return;
      const t = e.target as HTMLElement;
      if (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 未命中编号给出可见反馈
  const doSearch = () => {
    const v = inputRef.current?.value.trim() ?? "";
    if (!v) return;
    if (onSearch(v)) {
      if (inputRef.current) inputRef.current.value = "";
      setHint(null);
    } else {
      setHint(`未找到「${v}」——编号形如 M03 / M03-F15`);
      window.setTimeout(() => setHint(null), 3000);
    }
  };
  return (
    <div className="topbar">
      <span className="logo">Swan 功能地图</span>
      <input
        ref={inputRef}
        className="search"
        placeholder="搜索编号/用例(按 / 聚焦):M01-F03、launch-POS-001 或用例标题"
        onKeyDown={(e) => {
          if (e.key === "Enter") doSearch();
        }}
      />
      {hint && <span className="tag tag-warn" style={{ whiteSpace: "nowrap" }}>{hint}</span>}
      <button
        className="icon-btn"
        title={theme === "light" ? "切换到暗色" : "切换到亮色"}
        onClick={() => setTheme(toggleTheme())}
      >
        {theme === "light" ? <IconMoon /> : <IconSun />}
      </button>
    </div>
  );
}
